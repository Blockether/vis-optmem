"""The personal memory and the memory for everyone stay apart."""

import json
from pathlib import Path

import pytest

from vis_optmem import memo as memo_module
from vis_optmem.memo import EVERYONE, Memo, MemoryNotSet, status

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sqlite_store.py"


@pytest.fixture
def shared(tmp_path):
    return tmp_path / "everyone"


@pytest.fixture
def both(folder, shared):
    tool = Memo(folder, everyone_directory=shared)
    tool.init()
    tool.init(scope=EVERYONE)
    return tool


def test_each_memory_keeps_its_own_notes(both, folder, shared):
    assert both.note("my fact").id == 0
    saved = both.note("team fact", scope=EVERYONE)
    assert (saved.id, saved.scope) == (0, EVERYONE)
    assert saved.text == "Saved as #0 in the memory for everyone."
    assert [line.split(" ", 2)[2] for line in both.wake().lines] == ["my fact"]
    assert both.wake(scope=EVERYONE).lines[0].endswith(" team fact")
    assert both.recall("fact").matches[0].endswith(" my fact")
    assert both.recall("team").total == 0
    assert both.recall("team", scope=EVERYONE).total == 1
    assert (folder / "LOG.txt").exists() and (shared / "LOG.txt").exists()


def test_next_steps_name_the_memory_for_everyone(both):
    both.note("team fact 0", scope=EVERYONE)
    saved = both.note("team fact 1", scope=EVERYONE)
    assert saved.request.text.endswith(
        'Next: await memo.nap("0-1", "<your line>", scope="everyone")'
    )
    assert both.nap(scope=EVERYONE).request.block == "0-1"
    assert both.nap().request is None
    napped = both.nap("0-1", "two team facts", scope=EVERYONE)
    assert (napped.saved, napped.scope) == ("0-1", EVERYONE)
    dropped = both.forget("0-1", scope=EVERYONE)
    assert dropped.text.endswith('Call memo.nap(scope="everyone") to write it again.')


def test_a_personal_read_leads_to_the_memory_for_everyone(both):
    both.note("my fact")
    personal = both.wake()
    assert personal.is_awake
    assert personal.text.splitlines()[-1] == (
        'Next, read the memory for everyone: await memo.wake(scope="everyone")'
    )
    assert both.wake(scope=EVERYONE).text.splitlines()[-1] == "You are awake."
    first = both.wake(scope=EVERYONE).text
    assert 'memo.note("<one line>", scope="everyone")' in first


def test_a_memory_for_everyone_needs_a_place(memo, folder, shared):
    assert memo.wake().text.endswith("You are awake.")
    with pytest.raises(MemoryNotSet, match="MEMORY_EVERYONE_DIR"):
        memo.note("team fact", scope=EVERYONE)
    assert status(scope=EVERYONE) == {"store": "unset"}
    missing = Memo(folder, everyone_directory=shared)
    with pytest.raises(FileNotFoundError, match=r'memo.init\(scope="everyone"\)'):
        missing.wake(scope=EVERYONE)
    assert status(shared, scope=EVERYONE)["store"] == "missing"


def test_the_environment_chooses_the_memory_for_everyone(folder, shared, monkeypatch):
    monkeypatch.setenv("MEMORY_DIR", str(folder))
    monkeypatch.setenv("MEMORY_EVERYONE_DIR", str(shared))
    tool = Memo()
    assert tool.init(scope=EVERYONE).text.endswith(
        "one memory for everyone who reaches it."
    )
    tool.note("team fact", scope=EVERYONE)
    assert status(scope=EVERYONE)["memories"] == 1
    assert not folder.exists()


def test_a_custom_store_can_keep_the_memory_for_everyone(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(memo_module, "_loaded", {})
    database = tmp_path / "everyone.db"
    monkeypatch.setenv("MEMORY_EVERYONE_STORE", f"{EXAMPLE}:SqliteStore")
    monkeypatch.setenv(
        "MEMORY_EVERYONE_STORE_CONFIG", json.dumps({"path": str(database)})
    )
    tool = Memo(folder)
    tool.init()
    tool.init(scope=EVERYONE)
    tool.note("team fact", scope=EVERYONE)
    assert database.exists()
    assert tool.recall("team", scope=EVERYONE).total == 1
    assert tool.recall("team").total == 0
    monkeypatch.setenv("MEMORY_EVERYONE_STORE_CONFIG", "[]")
    monkeypatch.setattr(memo_module, "_loaded", {})
    with pytest.raises(ValueError, match="MEMORY_EVERYONE_STORE_CONFIG must be"):
        tool.wake(scope=EVERYONE)
    monkeypatch.delenv("MEMORY_EVERYONE_STORE_CONFIG")
    monkeypatch.setenv("MEMORY_EVERYONE_STORE", "no_such_store")
    with pytest.raises(ValueError, match="MEMORY_EVERYONE_STORE=no_such_store"):
        tool.wake(scope=EVERYONE)


def test_the_two_memories_cannot_share_a_folder(folder, monkeypatch):
    tool = Memo(folder, everyone_directory=folder)
    with pytest.raises(ValueError, match="its own folder"):
        tool.init(scope=EVERYONE)
    monkeypatch.setenv("MEMORY_DIR", str(folder))
    assert status(folder, scope=EVERYONE)["store"] == "unavailable"


def test_an_unknown_scope_is_refused(memo):
    with pytest.raises(ValueError, match="'personal' or 'everyone'"):
        memo.wake(scope="team")
    assert status(scope="team")["store"] == "unavailable"
