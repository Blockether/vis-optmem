"""Two memories say the same fact only with the same names. protocol.js uses the same fixture."""

import json
from pathlib import Path

import pytest

from vis_optmem.names import names, same_names

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "names.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["names"], ids=lambda case: case["text"])
def test_names_are_the_technical_tokens_environments_and_proper_names(case):
    assert sorted(names(case["text"])) == case["names"]


@pytest.mark.parametrize("case", FIXTURE["same_names"], ids=lambda case: case["a"])
def test_same_names_separates_near_facts(case):
    assert same_names(case["a"], case["b"]) is case["same"]
    assert same_names(case["b"], case["a"]) is case["same"]
