"""The personal memory and the team memory stay apart."""

import json
from pathlib import Path

import pytest

from vis_optmem import memo as memo_module
from vis_optmem.memo import TEAM, Memo, MemoryNotSet, status

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sqlite_store.py"


@pytest.fixture
def shared(tmp_path):
    return tmp_path / "team"


@pytest.fixture
def both(folder, shared):
    tool = Memo(folder, team_directory=shared)
    tool.init()
    tool.init(scope=TEAM)
    return tool


def test_each_memory_keeps_its_own_notes(both, folder, shared):
    assert both.note("my fact").id == 0
    saved = both.note("team fact", scope=TEAM)
    assert (saved.id, saved.scope) == (0, TEAM)
    assert saved.text == "Saved as #0 in the team memory."
    assert [line.split(" ", 2)[2] for line in both.wake().lines] == ["my fact"]
    assert both.wake(scope=TEAM).lines[0].endswith(" team fact")
    assert both.recall("fact").matches[0].endswith(" my fact")
    assert both.recall("team").total == 0
    assert both.recall("team", scope=TEAM).total == 1
    assert (folder / "LOG.txt").exists() and (shared / "LOG.txt").exists()


def test_team_notes_ask_for_no_summary_but_a_job_can_write_them(both, tmp_path):
    # The team memory is searched, not read, so agents spend no tokens on its summaries.
    both.note("team fact 0", scope=TEAM)
    saved = both.note("team fact 1", scope=TEAM)
    assert (saved.request, saved.text) == (None, "Saved as #1 in the team memory.")
    source = tmp_path / "old.txt"
    source.write_text("2999-01-01 a\n2999-01-02 b\n", encoding="utf-8")
    imported = both.import_memories(str(source), scope=TEAM)
    assert imported.due == 3 and imported.text == "Imported 2 memories, #2 to #3."
    request = both.nap(scope=TEAM).request
    assert request.block == "0-1"
    assert request.text.endswith(
        'Next: await memo.nap("0-1", "<your line>", scope="team")'
    )
    assert both.nap().request is None
    napped = both.nap("0-1", "two team facts", scope=TEAM)
    assert (napped.saved, napped.scope) == ("0-1", TEAM)
    dropped = both.forget("0-1", scope=TEAM)
    assert dropped.text.endswith('Call memo.nap(scope="team") to write it again.')


def test_a_personal_read_does_not_lead_to_the_team_memory(both):
    both.note("my fact")
    personal = both.wake()
    assert personal.is_awake
    assert personal.text.splitlines()[-1] == "You are awake."
    first = both.wake(scope=TEAM).text
    assert 'memo.note("<one line>", scope="team")' in first


def test_a_memory_for_team_needs_a_place(memo, folder, shared):
    assert memo.wake().text.endswith("You are awake.")
    with pytest.raises(MemoryNotSet, match="MEMORY_TEAM_DIR"):
        memo.note("team fact", scope=TEAM)
    assert status(scope=TEAM) == {"store": "unset"}
    missing = Memo(folder, team_directory=shared)
    with pytest.raises(FileNotFoundError, match=r'memo.init\(scope="team"\)'):
        missing.wake(scope=TEAM)
    assert status(shared, scope=TEAM)["store"] == "missing"


def test_the_environment_chooses_the_memory_for_team(folder, shared, monkeypatch):
    monkeypatch.setenv("MEMORY_DIR", str(folder))
    monkeypatch.setenv("MEMORY_TEAM_DIR", str(shared))
    tool = Memo()
    assert tool.init(scope=TEAM).text.endswith("one team memory who reaches it.")
    tool.note("team fact", scope=TEAM)
    assert status(scope=TEAM)["memories"] == 1
    assert not folder.exists()


def test_a_custom_store_can_keep_the_memory_for_team(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(memo_module, "_loaded", {})
    database = tmp_path / "team.db"
    monkeypatch.setenv("MEMORY_TEAM_STORE", f"{EXAMPLE}:SqliteStore")
    monkeypatch.setenv("MEMORY_TEAM_STORE_CONFIG", json.dumps({"path": str(database)}))
    tool = Memo(folder)
    tool.init()
    tool.init(scope=TEAM)
    tool.note("team fact", scope=TEAM)
    assert database.exists()
    assert tool.recall("team", scope=TEAM).total == 1
    assert tool.recall("team").total == 0
    monkeypatch.setenv("MEMORY_TEAM_STORE_CONFIG", "[]")
    monkeypatch.setattr(memo_module, "_loaded", {})
    with pytest.raises(ValueError, match="MEMORY_TEAM_STORE_CONFIG must be"):
        tool.wake(scope=TEAM)
    monkeypatch.delenv("MEMORY_TEAM_STORE_CONFIG")
    monkeypatch.setenv("MEMORY_TEAM_STORE", "no_such_store")
    with pytest.raises(ValueError, match="MEMORY_TEAM_STORE=no_such_store"):
        tool.wake(scope=TEAM)


def test_the_two_memories_cannot_share_a_folder(folder, monkeypatch):
    tool = Memo(folder, team_directory=folder)
    with pytest.raises(ValueError, match="its own folder"):
        tool.init(scope=TEAM)
    monkeypatch.setenv("MEMORY_DIR", str(folder))
    assert status(folder, scope=TEAM)["store"] == "unavailable"


def test_an_unknown_scope_is_refused(memo):
    with pytest.raises(ValueError, match="'personal' or 'team'"):
        memo.wake(scope="company")
    assert status(scope="company")["store"] == "unavailable"
