"""Shared fixtures: a new memory folder for each test."""

import pytest

from vis_optmem.memo import Memo


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
