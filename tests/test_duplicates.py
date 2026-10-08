"""memo.note refuses a fact that a memory already says, because memories never change."""

import pytest

from vis_optmem.memo import TEAM, DuplicateMemory, Memo
from vis_optmem.store import FileStore


def test_a_repeated_fact_is_refused_with_the_memory_that_says_it(memo):
    memo.note("Use uv, not Poetry, for Python projects.")
    with pytest.raises(DuplicateMemory) as refused:
        memo.note("For Python projects use uv instead of Poetry.")
    lines = str(refused.value).splitlines()
    assert lines[0] == "Not saved: this memory already says it:"
    assert lines[1].startswith("#0 ") and lines[1].endswith("for Python projects.")
    assert 'memo.note("<your line>", force=True)' in lines[2]
    assert memo._store().count() == 1


def test_a_new_fact_a_new_number_or_force_is_saved(memo):
    memo.note("Postgres listens on port 5432.")
    assert memo.note("Postgres listens on port 6543.").id == 1
    assert memo.note("Run schema migrations before the deploy.").id == 2
    assert memo.note("Postgres listens on port 5432.", force=True).id == 3


def test_each_memory_checks_only_its_own_notes(folder, tmp_path):
    tool = Memo(folder, team_directory=tmp_path / "team")
    tool.init()
    tool.init(scope=TEAM)
    tool.note("Use uv, not Poetry, for Python projects.")
    assert tool.note("Use uv, not Poetry, for Python projects.", scope=TEAM).id == 0
    with pytest.raises(DuplicateMemory, match=r'scope="team"\)'):
        tool.note("use uv not poetry for python projects", scope=TEAM)


class NearStore(FileStore):
    """A store whose search finds nothing: the check then saves the note."""

    def search(self, query, limit):
        return []


def test_the_check_compares_only_what_the_store_search_finds(folder):
    tool = Memo(store=NearStore(folder))
    tool.init()
    tool.note("Use uv for Python projects.")
    assert tool.note("Use uv for Python projects.").id == 1
