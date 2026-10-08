"""scope="all" searches the personal memory and the team memory in one call."""

import pytest

from vis_optmem.memo import ALL, TEAM, Memo


@pytest.fixture
def both(folder, tmp_path):
    tool = Memo(folder, team_directory=tmp_path / "team")
    tool.init()
    tool.init(scope=TEAM)
    tool.note("Use uv for my scripts.")
    tool.note("Postgres migrations need a lock timeout.", scope=TEAM)
    tool.note("Run schema migrations before the deploy.", scope=TEAM)
    return tool


def test_all_finds_matches_in_both_memories_with_their_names(both):
    found = both.recall(r"uv|migrations", scope=ALL)
    assert found.total == 3
    assert [line.split(" ", 2)[:2] for line in found.matches] == [
        ["[personal]", "#0"],
        ["[team]", "#0"],
        ["[team]", "#1"],
    ]
    assert found.text.splitlines()[-1] == "3 matches."
    assert both.recall(r"uv").total == 1
    assert both.recall(r"uv", scope=TEAM).total == 0


def test_all_mixes_the_related_memories_of_both(both):
    found = both.recall(about="scripts schema migrations", scope=ALL)
    assert [line.split(" ", 1)[0] for line in found.related] == [
        "[personal]",
        "[team]",
        "[team]",
    ]
    assert found.scope == ALL


def test_all_without_a_memory_for_team_searches_the_personal_one(memo):
    memo.note("Use uv for my scripts.")
    found = memo.recall("uv", scope=ALL)
    assert found.matches[0].startswith("[personal] #0 ")
    assert found.text.splitlines()[0] == (
        "Only the personal memory: no team memory is set."
    )


def test_all_keeps_the_newest_matches_of_both_in_one_print(both):
    for index in range(40):
        both.note(f"long note {index} " + "x" * 250, scope=TEAM)
        both.note(f"long note {index} " + "y" * 250)
    found = both.recall("long note", scope=ALL)
    assert found.total == 80 and len(found.matches) < 80
    assert len(found.text.encode("utf-8")) <= 8000
    assert {line.split(" ", 1)[0] for line in found.matches} == {
        "[personal]",
        "[team]",
    }


@pytest.mark.parametrize("method", ["wake", "note", "nap", "zoom", "forget", "init"])
def test_only_recall_takes_all(both, method):
    args = {"note": ("a fact",), "zoom": ("0-1",), "forget": ("0-1",)}.get(method, ())
    with pytest.raises(ValueError, match="works only with memo.recall"):
        getattr(both, method)(*args, scope=ALL)
