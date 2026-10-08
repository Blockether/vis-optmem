"""A custom MemoryStore gives the same memory as the OptMem files."""

import json
import shutil
import sys
import threading
import types
from pathlib import Path

import pytest
from conftest import fill

from vis_optmem import memo as memo_module
from vis_optmem.memo import Memo, load_store, status
from vis_optmem.store import MemoryStore

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sqlite_store.py"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Forget built stores and the store settings of the process."""
    monkeypatch.setattr(memo_module, "_loaded", {})
    for name in ("MEMORY_DIR", "MEMORY_STORE", "MEMORY_STORE_CONFIG"):
        monkeypatch.delenv(name, raising=False)


def sqlite_store(path):
    return load_store(f"{EXAMPLE}:SqliteStore", {"path": str(path)})


def run(tool, source):
    """Every memo operation, with the text that each one gives."""
    texts = [tool.init().text]
    fill(tool, 21)
    texts += [tool.wake().text, tool.recall(r"memory 1\d").text, tool.zoom("0-15").text]
    texts += [tool.forget("0-3").text, tool.nap().text]
    fill(tool, 0)
    texts += [tool.config({"WAKE_LINES": 4}).text, tool.wake().text]
    texts += [tool.import_memories(str(source)).text, tool.nap().text]
    return [text.replace(str(tool._store().location), "<place>") for text in texts]


def test_the_sqlite_example_gives_the_same_memory_as_the_files(tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("2999-01-01 first imported\n2999-01-02 second imported\n")
    files = run(Memo(tmp_path / "memory"), source)
    database = run(Memo(store=sqlite_store(tmp_path / "memory.db")), source)
    assert database == files
    assert "You are awake." in files[1]


def test_memory_store_selects_a_store_file_in_the_workspace(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    shutil.copy(EXAMPLE, project / "tools" / "store.py")
    monkeypatch.setenv("MEMORY_STORE", "tools/store.py:SqliteStore")
    monkeypatch.setenv(
        "MEMORY_STORE_CONFIG", json.dumps({"path": str(tmp_path / "m.db")})
    )
    tool = Memo(base=lambda: project)
    assert tool.init().path == f"sqlite:{tmp_path / 'm.db'}"
    tool.note("The team keeps memory in one database.")
    assert status(base=lambda: project) == {
        "memories": 1,
        "summaries_due": 0,
        "max_bytes": 280,
    }
    assert tool._store() is Memo(base=lambda: project)._store()


def test_memory_store_selects_a_module_and_an_installed_name(tmp_path, monkeypatch):
    module = types.ModuleType("team_memory")
    module.make = lambda path: sqlite_store(path)
    monkeypatch.setitem(sys.modules, "team_memory", module)
    monkeypatch.setenv("MEMORY_STORE", "team_memory:make")
    monkeypatch.setenv(
        "MEMORY_STORE_CONFIG", json.dumps({"path": str(tmp_path / "a.db")})
    )
    assert Memo().init().is_new

    point = types.SimpleNamespace(name="team", load=lambda: module.make)

    def entry_points(*, group, name=None):
        assert group == "vis_optmem.stores"
        return [point] if name in (None, "team") else []

    monkeypatch.setattr(memo_module.metadata, "entry_points", entry_points)
    monkeypatch.setenv("MEMORY_STORE", "team")
    monkeypatch.setenv(
        "MEMORY_STORE_CONFIG", json.dumps({"path": str(tmp_path / "b.db")})
    )
    assert Memo().init().path == f"sqlite:{tmp_path / 'b.db'}"
    monkeypatch.setenv("MEMORY_STORE", "other")
    with pytest.raises(
        ValueError, match="names no installed store. Installed stores: team"
    ):
        Memo().init()


def test_an_explicit_folder_wins_over_memory_store(folder, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "missing_module:make")
    assert Memo(folder).init().path == str(folder)


@pytest.mark.parametrize(
    ("reference", "config", "message"),
    [
        ("nowhere/store.py:Make", "", "no Python file at"),
        (f"{EXAMPLE}:Missing", "", "has no Missing"),
        (f"{EXAMPLE}:SqliteStore", "{bad", "MEMORY_STORE_CONFIG is not valid JSON"),
        (f"{EXAMPLE}:SqliteStore", "[1]", "must be a JSON object"),
        ("json:dumps", '{"obj": 1}', "made a str, not a MemoryStore"),
    ],
)
def test_a_wrong_store_setting_is_reported(
    tmp_path, monkeypatch, reference, config, message
):
    monkeypatch.setenv("MEMORY_STORE", reference)
    monkeypatch.setenv("MEMORY_STORE_CONFIG", config)
    with pytest.raises((ValueError, TypeError), match=message):
        Memo(base=lambda: tmp_path).wake()
    facts = status(base=lambda: tmp_path)
    assert facts["store"] == "unavailable"
    assert message in facts["error"]


def test_status_reports_an_unreachable_store():
    class Offline(MemoryStore):
        location = "dynamodb:team-memory"

        def exists(self):
            raise ConnectionError("no route to the database")

        create = overrides = write_overrides = count = entries = append = exists
        level_count = summary = put_summary = drop_summaries = exists

    assert status(store=Offline()) == {
        "store": "unreadable",
        "path": "dynamodb:team-memory",
        "error": "no route to the database",
    }


def test_sessions_that_write_at_once_get_different_ids(tmp_path):
    store = sqlite_store(tmp_path / "memory.db")
    store.create()
    ids = []

    def write():
        for _ in range(10):
            ids.append(store.append([("2026-01-01", "note")]))

    threads = [threading.Thread(target=write) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(ids) == list(range(40))
    assert [entry.id for entry in store.scan()] == list(range(40))
