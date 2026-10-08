"""MEMORY_DISABLED_TOOLS turns memo tools off, for example init and import."""

import pytest

from vis_optmem.memo import TOOLS, Memo, ToolDisabled, disabled_tools


def test_turned_off_tools_refuse_and_the_others_work(memo, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DISABLED_TOOLS", "init, import_memories")
    source = tmp_path / "old.txt"
    source.write_text("2024-01-01 a\n", encoding="utf-8")
    with pytest.raises(ToolDisabled, match=r"memo.init is turned off"):
        memo.init()
    with pytest.raises(ToolDisabled, match=r"memo.import_memories is turned off"):
        memo.import_memories(str(source))
    assert memo.note("a fact").id == 0
    assert memo.recall("fact").total == 1


def test_a_missing_memory_does_not_point_to_a_turned_off_init(folder, monkeypatch):
    monkeypatch.setenv("MEMORY_DISABLED_TOOLS", "init")
    with pytest.raises(FileNotFoundError, match="Ask the person who set up the memory"):
        Memo(folder).wake()
    assert not folder.exists()
    monkeypatch.delenv("MEMORY_DISABLED_TOOLS")
    with pytest.raises(FileNotFoundError, match=r"call memo.init\(\)"):
        Memo(folder).wake()


def test_the_python_argument_wins_over_the_environment(folder, monkeypatch):
    monkeypatch.setenv("MEMORY_DISABLED_TOOLS", "init")
    assert Memo(folder, disabled=()).init().is_new
    with pytest.raises(ToolDisabled):
        Memo(folder, disabled=["wake"]).wake()


def test_every_tool_can_be_turned_off(memo):
    for tool in TOOLS:
        with pytest.raises(ToolDisabled):
            getattr(Memo(memo._path(), disabled=[tool]), tool)("0-1")


def test_a_wrong_name_turns_nothing_off_and_fails_loudly(memo, monkeypatch):
    assert disabled_tools("init nap,,") == {"init", "nap"}
    assert disabled_tools() == frozenset()
    with pytest.raises(ValueError, match="imports is not a memo tool"):
        disabled_tools("imports")
    monkeypatch.setenv("MEMORY_DISABLED_TOOLS", "imports")
    with pytest.raises(ValueError, match="MEMORY_DISABLED_TOOLS: imports"):
        memo.wake()
