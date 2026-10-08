"""memo.recall finds memories word for word, by meaning, or both in one call."""

import pytest
from conftest import fill

from vis_optmem.memo import Memo
from vis_optmem.store import FileStore


def notes(memo):
    for line in [
        "Postgres migrations need a lock timeout.",
        "Use uv, not Poetry, for Python projects.",
        "Run schema migrations before the deploy.",
        "The deploy script lives in ops.",
    ]:
        memo.note(line)


def test_the_default_search_ranks_shared_and_rare_words_first(memo):
    notes(memo)
    found = memo.recall(about="how to migrate a database schema")
    assert [line.split(" ", 2)[2] for line in found.related] == [
        "Run schema migrations before the deploy.",
        "Postgres migrations need a lock timeout.",
    ]
    assert found.text.splitlines()[0] == (
        "Related to 'how to migrate a database schema', best first:"
    )
    assert (found.pattern, found.matches, found.total) == (None, (), 0)


def test_a_pattern_and_about_give_one_result_without_repeats(memo):
    notes(memo)
    found = memo.recall(r"postgres", about="migrations before deploy")
    assert found.total == 1 and found.matches[0].endswith("lock timeout.")
    assert found.related[0].endswith("before the deploy.")
    lines = found.text.splitlines()
    assert lines[:2] == [found.matches[0], "1 match."]
    assert lines.count(found.matches[0]) == 1
    nothing = memo.recall("gamma", about="kubernetes")
    assert nothing.text == "No match.\n\nNothing related to 'kubernetes'."


def test_a_recall_needs_a_pattern_or_about(memo):
    with pytest.raises(ValueError, match=r"about \(plain words\), or both"):
        memo.recall()
    with pytest.raises(ValueError, match="about is empty"):
        memo.recall(about="  ")
    assert memo.recall("x").text == "No match."


class MeaningStore(FileStore):
    """A store that knows that a database and Postgres are the same thing."""

    def search(self, query, limit):
        if "database" in query:
            return [self.entry(0)]
        return []


def test_a_store_can_search_by_meaning(folder):
    tool = Memo(store=MeaningStore(folder))
    tool.init()
    notes(tool)
    found = tool.recall(about="database")
    assert found.related == (tool._store().entry(0).line,)


def test_the_related_memories_leave_room_in_one_print(memo):
    fill(memo, 40, text="deploy note {} " + "x" * 200)
    found = memo.recall("deploy", about="deploy note")
    assert len(found.related) == 10
    assert len(found.text.encode("utf-8")) <= 8000
