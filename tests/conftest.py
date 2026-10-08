"""Shared fixtures: a new memory folder for each test."""

import pytest

from vis_optmem.memo import VARIABLES, Memo


@pytest.fixture(autouse=True)
def no_memory_settings(monkeypatch):
    """Keep the memory settings of the process out of every test."""
    for names in VARIABLES.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("MEMORY_DISABLED_TOOLS", raising=False)


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "memory"


@pytest.fixture
def memo(folder):
    tool = Memo(folder)
    tool.init()
    return tool


def fill(memo, count, text="memory {}"):
    """Save `count` memories and write every summary that comes due."""
    for index in range(count):
        memo.note(text.format(index))
        request = memo.nap().request
        while request is not None:
            request = memo.nap(request.block, f"summary {request.block}").request
