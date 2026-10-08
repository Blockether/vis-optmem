"""scope="all" searches the personal memory and the memory for everyone in one call."""

import pytest

from vis_optmem.memo import ALL, EVERYONE, Memo


@pytest.fixture
def both(folder, tmp_path):
    tool = Memo(folder, everyone_directory=tmp_path / "everyone")
    tool.init()
    tool.init(scope=EVERYONE)
    tool.note("Use uv for my scripts.")
    tool.note("Postgres migrations need a lock timeout.", scope=EVERYONE)
    tool.note("Run schema migrations before the deploy.", scope=EVERYONE)
    return tool


def test_all_finds_matches_in_both_memories_with_their_names(both):
    found = both.recall(r"uv|migrations", scope=ALL)
    assert found.total == 3
    assert [line.split(" ", 2)[:2] for line in found.matches] == [
        ["[personal]", "#0"],
        ["[everyone]", "#0"],
        ["[everyone]", "#1"],
    ]
    assert found.text.splitlines()[-1] == "3 matches."
    assert both.recall(r"uv").total == 1
    assert both.recall(r"uv", scope=EVERYONE).total == 0


def test_all_mixes_the_related_memories_of_both(both):
    found = both.recall(about="scripts schema migrations", scope=ALL)
    assert [line.split(" ", 1)[0] for line in found.related] == [
        "[personal]",
        "[everyone]",
        "[everyone]",
    ]
    assert found.scope == ALL


def test_all_without_a_memory_for_everyone_searches_the_personal_one(memo):
    memo.note("Use uv for my scripts.")
    found = memo.recall("uv", scope=ALL)
    assert found.matches[0].startswith("[personal] #0 ")
    assert found.text.splitlines()[0] == (
        "Only the personal memory: no memory for everyone is set."
    )


def test_all_keeps_the_newest_matches_of_both_in_one_print(both):
    for index in range(40):
        both.note(f"long note {index} " + "x" * 250, scope=EVERYONE)
        both.note(f"long note {index} " + "y" * 250)
    found = both.recall("long note", scope=ALL)
    assert found.total == 80 and len(found.matches) < 80
    assert len(found.text.encode("utf-8")) <= 8000
    assert {line.split(" ", 1)[0] for line in found.matches} == {
        "[personal]",
        "[everyone]",
    }


@pytest.mark.parametrize("method", ["wake", "note", "nap", "zoom", "forget", "init"])
def test_only_recall_takes_all(both, method):
    args = {"note": ("a fact",), "zoom": ("0-1",), "forget": ("0-1",)}.get(method, ())
    with pytest.raises(ValueError, match="works only with memo.recall"):
        getattr(both, method)(*args, scope=ALL)
